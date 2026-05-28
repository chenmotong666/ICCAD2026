module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n6,
    n7,
    n8,
    n20,
    n22,
    n21,
    n23
);

  input n0;
  input n1;
  input n2;
  input n3;
  input n4;
  input n5;
  input n6;
  input n7;
  input n8;
  output n20;
  output n22;
  output n21;
  output n23;

  wire \$and$testcase/test54/test54.v:9$4_Y , \$or$testcase/test54/test54.v:10$6_Y , \$xor$testcase/test54/test54.v:11$8_Y , __ded_n2_0__, __ded_n2_10__, __ded_n2_11__, __ded_n2_1__, __ded_n2_2__, __ded_n2_3__, __ded_n2_4__, __ded_n2_5__, __ded_n2_6__, __ded_n2_7__, __ded_n2_8__, __ded_n2_9__, n100, n101, n102, n103, n104, n105, n107, n108, n109, n110, n111, n114, n116, n120, n121, n122, n123, n124, n125, n126, n127, n128, n129, n130, n140, n141, n142;

  buf   ded_buf_0 (__ded_n2_0__, n2);
  buf   ded_buf_1 (__ded_n2_1__, n2);
  buf   ded_buf_2 (__ded_n2_2__, n2);
  buf   ded_buf_3 (__ded_n2_3__, n2);
  buf   ded_buf_4 (__ded_n2_4__, n2);
  buf   ded_buf_5 (__ded_n2_5__, n2);
  buf   ded_buf_6 (__ded_n2_6__, n2);
  buf   ded_buf_7 (__ded_n2_7__, n2);
  buf   ded_buf_8 (__ded_n2_8__, n2);
  buf   ded_buf_9 (__ded_n2_9__, n2);
  buf   ded_buf_10 (__ded_n2_10__, n2);
  buf   ded_buf_11 (__ded_n2_11__, n2);
  not   \$not$testcase/test54/test54.v:18$34  (n111, n4);
  not   \$not$testcase/test54/test54.v:21$36  (n114, n4);
  not   \$not$testcase/test54/test54.v:24$17  (n116, n5);
  and   \$and$testcase/test54/test54.v:37$30  (n140, n6, n7);
  and   \$and$testcase/test54/test54.v:13$10  (n107, __ded_n2_0__, n3);
  and   \$and$testcase/test54/test54.v:16$13  (n109, __ded_n2_1__, n3);
  and   \$and$testcase/test54/test54.v:17$14  (n110, __ded_n2_2__, n3);
  and   \$and$testcase/test54/test54.v:27$20  (n120, __ded_n2_3__, n3);
  and   \$and$testcase/test54/test54.v:28$21  (n121, __ded_n2_4__, n4);
  and   \$and$testcase/test54/test54.v:29$22  (n122, __ded_n2_5__, n5);
  and   \$and$testcase/test54/test54.v:30$23  (n123, __ded_n2_6__, n6);
  and   \$and$testcase/test54/test54.v:31$24  (n124, __ded_n2_7__, n7);
  and   \$and$testcase/test54/test54.v:32$25  (n125, __ded_n2_8__, n8);
  and   \$and$testcase/test54/test54.v:33$26  (n126, __ded_n2_9__, n3);
  and   \$and$testcase/test54/test54.v:34$27  (n127, __ded_n2_10__, n4);
  and   \$and$testcase/test54/test54.v:5$1  (n100, __ded_n2_11__, n3);
  or    \$or$testcase/test54/test54.v:38$31  (n141, n140, n8);
  or    \$or$testcase/test54/test54.v:14$11  (n108, n107, n5);
  or    \$or$testcase/test54/test54.v:35$28  (n128, n120, n121);
  xor   \$xor$testcase/test54/test54.v:36$29  (n129, n122, n123);
  or    \$or$testcase/test54/test54.v:23$15  (n22, n114, n100);
  or    \$or$testcase/test54/test54.v:6$2  (n101, n100, n4);
  not   \$not$testcase/test54/test54.v:39$37  (n142, n141);
  xor   \$xor$testcase/test54/test54.v:15$12  (n21, n108, n6);
  and   \$and$testcase/test54/test54.v:41$32  (n130, n129, n128);
  not   \$not$testcase/test54/test54.v:7$33  (n102, n101);
  xor   \$xor$testcase/test54/test54.v:8$3  (n103, n102, n5);
  and   \$and$testcase/test54/test54.v:9$4  (\$and$testcase/test54/test54.v:9$4_Y , n103, n6);
  not   \$not$testcase/test54/test54.v:9$5  (n104, \$and$testcase/test54/test54.v:9$4_Y );
  or    \$or$testcase/test54/test54.v:10$6  (\$or$testcase/test54/test54.v:10$6_Y , n104, n7);
  not   \$not$testcase/test54/test54.v:10$7  (n105, \$or$testcase/test54/test54.v:10$6_Y );
  xor   \$xor$testcase/test54/test54.v:11$8  (\$xor$testcase/test54/test54.v:11$8_Y , n105, n8);
  not   \$not$testcase/test54/test54.v:11$9  (n20, \$xor$testcase/test54/test54.v:11$8_Y );

  assign n23 = n5;

endmodule
