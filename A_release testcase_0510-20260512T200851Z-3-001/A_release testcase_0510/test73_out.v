module top (
    n0,
    n1,
    n2,
    n3,
    n4,
    n5,
    n7,
    n8,
    n6,
    n12,
    n11,
    n9,
    n10
);

  input n0;
  input n1;
  input [3:0] n2;
  input [3:0] n3;
  input n4;
  input n5;
  output n7;
  output n8;
  output n6;
  output n12;
  output n11;
  output n9;
  output n10;

  wire \$and$testcase/test73/test73.v:20$4_Y , \$and$testcase/test73/test73.v:31$17_Y , \$and$testcase/test73/test73.v:35$22_Y , \$auto$rtlil.cc:3255:Not$42 , __fo_n2_2__0__, __fo_n2_2__1__, __fo_n2_2__2__, __fo_n2_2__3__, __fo_n3_2__0__, __fo_n3_2__1__, __fo_n3_2__2__, __fo_n5_0__, __fo_n5_1__, __fo_n5_2__, __fo_n5_3__, n13, n14, n15, n16, n17, n18, n19, n30, n31, n40, n41, n42, n43, n44, n45, n46, n47, n48, n49, n56, n57, n63, n65, n80, n81, n82;

  and   \$and$testcase/test73/test73.v:52$34  (n80, n2[0], n2[1]);
  buf   fo_buf_0 (__fo_n2_2__0__, n2[2]);
  buf   fo_buf_1 (__fo_n2_2__1__, n2[2]);
  buf   fo_buf_2 (__fo_n2_2__2__, n2[2]);
  buf   fo_buf_3 (__fo_n2_2__3__, n2[2]);
  and   \$and$testcase/test73/test73.v:16$1  (n13, n2[0], n3[0]);
  and   \$and$testcase/test73/test73.v:25$11  (n30, n2[1], n3[1]);
  buf   fo_buf_4 (__fo_n3_2__0__, n3[2]);
  buf   fo_buf_5 (__fo_n3_2__1__, n3[2]);
  buf   fo_buf_6 (__fo_n3_2__2__, n3[2]);
  not   \$not$testcase/test73/test73.v:46$38  (n63, n4);
  buf   fo_buf_7 (__fo_n5_0__, n5);
  buf   fo_buf_8 (__fo_n5_1__, n5);
  buf   fo_buf_9 (__fo_n5_2__, n5);
  buf   fo_buf_10 (__fo_n5_3__, n5);
  or    \$or$testcase/test73/test73.v:53$35  (n81, n80, n3[0]);
  or    \$or$testcase/test73/test73.v:17$2  (n14, n13, n4);
  and   \$and$testcase/test73/test73.v:28$14  (n40, __fo_n2_2__0__, __fo_n3_2__0__);
  and   \$and$testcase/test73/test73.v:32$19  (n44, __fo_n2_2__0__, __fo_n3_2__0__);
  and   \$and$testcase/test73/test73.v:36$24  (n48, __fo_n2_2__1__, __fo_n3_2__1__);
  xor   \$xor$testcase/test73/test73.v:30$16  (n42, __fo_n2_2__3__, __fo_n3_2__1__);
  xor   \$xor$testcase/test73/test73.v:34$21  (n46, __fo_n2_2__3__, __fo_n3_2__2__);
  and   \$and$testcase/test73/test73.v:31$17  (\$and$testcase/test73/test73.v:31$17_Y , __fo_n2_2__0__, __fo_n5_0__);
  and   \$and$testcase/test73/test73.v:35$22  (\$and$testcase/test73/test73.v:35$22_Y , __fo_n2_2__1__, __fo_n5_0__);
  or    \$or$testcase/test73/test73.v:29$15  (n41, __fo_n2_2__1__, __fo_n5_1__);
  or    \$or$testcase/test73/test73.v:33$20  (n45, __fo_n2_2__2__, __fo_n5_2__);
  or    \$or$testcase/test73/test73.v:37$25  (n49, __fo_n2_2__2__, __fo_n5_2__);
  xor   \$xor$testcase/test73/test73.v:26$12  (n31, n30, __fo_n5_3__);
  not   \$not$testcase/test73/test73.v:54$40  (n82, n81);
  not   \$not$testcase/test73/test73.v:18$36  (n15, n14);
  not   \$not$testcase/test73/test73.v:31$18  (n43, \$and$testcase/test73/test73.v:31$17_Y );
  not   \$not$testcase/test73/test73.v:35$23  (n47, \$and$testcase/test73/test73.v:35$22_Y );
  or    \$or$testcase/test73/test73.v:38$26  (n56, n40, n41);
  or    \$or$testcase/test73/test73.v:27$13  (n7, n31, n4);
  or    \$or$testcase/test73/test73.v:19$3  (n16, n15, __fo_n5_1__);
  or    \$or$testcase/test73/test73.v:39$27  (n57, n56, n42);
  and   \$and$testcase/test73/test73.v:20$4  (\$and$testcase/test73/test73.v:20$4_Y , n16, n2[1]);
  or    \$or$testcase/test73/test73.v:40$28  (n8, n57, n43);
  not   \$not$testcase/test73/test73.v:20$5  (n17, \$and$testcase/test73/test73.v:20$4_Y );
  and   \$and$testcase/test73/test73.v:49$32  (n65, n31, n8);
  or    \$or$testcase/test73/test73.v:21$6  (\$auto$rtlil.cc:3255:Not$42 , n17, n3[1]);
  dff g34 (.Q(n11), .RN(n1), .SN(1'b1), .CK(n0), .D(n65));
  not   \$not$testcase/test73/test73.v:21$7  (n18, \$auto$rtlil.cc:3255:Not$42 );
  not   \$not$testcase/test73/test73.v:22$9  (n19, \$auto$rtlil.cc:3255:Not$42 );
  xor   \$xor$testcase/test73/test73.v:23$10  (n6, n19, __fo_n2_2__2__);
  xor   \$xor$testcase/test73/test73.v:51$33  (n12, n6, n7);

  assign n9 = 1'b1;
  assign n10 = n4;

endmodule

(* blackbox *) module dff(input RN, input SN, input CK, input D, output Q);
endmodule
