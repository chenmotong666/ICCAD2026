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
    n31,
    n32,
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
  output n31;
  output n32;
  output n23;

  wire \$and$testcase/test66/test66.v:10$4_Y , \$or$testcase/test66/test66.v:11$6_Y , \$xor$testcase/test66/test66.v:12$8_Y , __fo_n2_0__, __fo_n2_1__, __fo_n2_2__, __fo_n2_3__, n100, n101, n102, n103, n104, n105, n107, n108, n109, n110, n111, n114, n116, n120, n121, n122, n123, n124, n125, n126, n127, n128, n129, n130, n140, n141, n142;

  buf   fo_buf_0 (__fo_n2_0__, n2);
  buf   fo_buf_1 (__fo_n2_1__, n2);
  buf   fo_buf_2 (__fo_n2_2__, n2);
  buf   fo_buf_3 (__fo_n2_3__, n2);
  not   \$not$testcase/test66/test66.v:19$34  (n111, n4);
  not   \$not$testcase/test66/test66.v:22$36  (n114, n4);
  not   \$not$testcase/test66/test66.v:25$17  (n116, n5);
  and   \$and$testcase/test66/test66.v:38$30  (n140, n6, n7);
  and   \$and$testcase/test66/test66.v:14$10  (n107, __fo_n2_0__, n3);
  and   \$and$testcase/test66/test66.v:17$13  (n109, __fo_n2_0__, n3);
  and   \$and$testcase/test66/test66.v:18$14  (n110, __fo_n2_0__, n3);
  and   \$and$testcase/test66/test66.v:28$20  (n120, __fo_n2_1__, n3);
  and   \$and$testcase/test66/test66.v:29$21  (n121, __fo_n2_1__, n4);
  and   \$and$testcase/test66/test66.v:30$22  (n122, __fo_n2_1__, n5);
  and   \$and$testcase/test66/test66.v:31$23  (n123, __fo_n2_2__, n6);
  and   \$and$testcase/test66/test66.v:32$24  (n124, __fo_n2_2__, n7);
  and   \$and$testcase/test66/test66.v:33$25  (n125, __fo_n2_2__, n8);
  and   \$and$testcase/test66/test66.v:34$26  (n126, __fo_n2_3__, n3);
  and   \$and$testcase/test66/test66.v:35$27  (n127, __fo_n2_3__, n4);
  and   \$and$testcase/test66/test66.v:6$1  (n100, __fo_n2_3__, n3);
  or    \$or$testcase/test66/test66.v:39$31  (n141, n140, n8);
  or    \$or$testcase/test66/test66.v:15$11  (n108, n107, n5);
  or    \$or$testcase/test66/test66.v:36$28  (n128, n120, n121);
  xor   \$xor$testcase/test66/test66.v:37$29  (n129, n122, n123);
  or    \$or$testcase/test66/test66.v:24$15  (n22, n114, n100);
  or    \$or$testcase/test66/test66.v:7$2  (n101, n100, n4);
  not   \$not$testcase/test66/test66.v:40$37  (n142, n141);
  xor   \$xor$testcase/test66/test66.v:16$12  (n21, n108, n6);
  and   \$and$testcase/test66/test66.v:42$32  (n130, n129, n128);
  not   \$not$testcase/test66/test66.v:8$33  (n102, n101);
  dff g72 (.Q(n31), .RN(n1), .SN(1'b1), .CK(n0), .D(n130));
  xor   \$xor$testcase/test66/test66.v:9$3  (n103, n102, n5);
  and   \$and$testcase/test66/test66.v:10$4  (\$and$testcase/test66/test66.v:10$4_Y , n103, n6);
  not   \$not$testcase/test66/test66.v:10$5  (n104, \$and$testcase/test66/test66.v:10$4_Y );
  or    \$or$testcase/test66/test66.v:11$6  (\$or$testcase/test66/test66.v:11$6_Y , n104, n7);
  not   \$not$testcase/test66/test66.v:11$7  (n105, \$or$testcase/test66/test66.v:11$6_Y );
  xor   \$xor$testcase/test66/test66.v:12$8  (\$xor$testcase/test66/test66.v:12$8_Y , n105, n8);
  not   \$not$testcase/test66/test66.v:12$9  (n20, \$xor$testcase/test66/test66.v:12$8_Y );
  dff g73 (.Q(n32), .RN(n1), .SN(1'b1), .CK(n0), .D(n20));

  assign n23 = n5;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
